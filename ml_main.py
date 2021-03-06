try:
    from biosuite.helper_functions import check_dependencies
except ImportError:
    raise ImportError("Please install biosuite: https://github.com/OliverPalmer/biosuite")

#check_dependencies()

import matplotlib.pyplot as plt
import numpy as np
import collections
import subprocess
import tarfile
import random
import sys
import os
import re

from biosuite import BioSession
from biosuite.machine_learning import MLController, Classifier
from biosuite.helper_functions import inform, warn, get_date_time
from biosuite.pipeline import Pipeline, Operation
from biosuite.gene import Gene

from sklearn import svm
from sklearn.ensemble import RandomForestClassifier
from sklearn.naive_bayes import GaussianNB
from sklearn.ensemble import AdaBoostClassifier
from sklearn.neighbors import KNeighborsClassifier



class AddGenesToSession(Operation):
    
    def __init__(self, name="AddGenesToSession"):
        Operation.__init__(self, name)
    
    def __call__(self):
        print "Adding genes to session."
        added = 0
        encountered_dict = {}
        for line in open(self.pipeline.communities_filename, "r"):
            gene_id, cluster = line.rstrip().split()
            if gene_id in encountered_dict:
                continue
            encountered_dict[gene_id] = True
            protein = Gene(gene_id)
            self.pipeline.biosession.add_protein(protein)
            added += 1
        print "Added {} genes to session".format(added)

class AddTrainingPositiveStatus(Operation):
    
    def __init__(self, name="AddTrainingPositiveStatus"):
        Operation.__init__(self, name)
    
    def __call__(self):
        print "Adding training status to training positive genes."
        training_positive_genes = []
        for line in open(self.pipeline.train_positives_fname):
            gene_name = line.rstrip()
            try:
                gene = self.pipeline.biosession.get_protein(gene_name, silent=True)
                gene.add_property("is_training", 1)
                gene.add_property("ml_label", 1)
                training_positive_genes.append(gene)
            except AttributeError:
                pass
                #print "missing gene:",gene_name
        self.pipeline.add_resource("training_positive_genes", training_positive_genes)
        n_added = len(training_positive_genes)
        print "Added {} training positive genes".format(n_added)
        if n_added == 0:
            raise Exception("No training positive genes added to session")

class AddTrainingNegativeStatus(Operation):
    
    def __init__(self, name="AddTrainingNegativeStatus"):
        Operation.__init__(self, name)
    
    def __call__(self):
        print "Adding training status to training negative genes."
        #These genes end up as training negatives given the random seed but they are SNP containing genes in patients
        #therefore we want to put them in the test set so that we can get a score for them
        manually_excluded = ["00000361917", "00000371398", "00000341141", "00000410312"]
        
        all_gene_ids_less_mitos = set(self.pipeline.biosession.proteins).difference(set(self.pipeline.training_positive_genes))
        all_gene_ids_less_mitos = list(all_gene_ids_less_mitos.difference(manually_excluded) )
        random_background_snps = random.sample(all_gene_ids_less_mitos, len(self.pipeline.training_positive_genes))

        for gene in random_background_snps:
            gene.add_property("is_training", 1)
            gene.add_property("ml_label", 0)
        self.pipeline.add_resource("training_negative_genes", random_background_snps)

class AddTestStatus(Operation):
    
    def __init__(self, name="AddTestStatus"):
        Operation.__init__(self, name)
    
    def __call__(self):
        print "Adding test status to genes."
        training_positive_genes = set(self.pipeline.training_positive_genes)
        training_negative_genes = set(self.pipeline.training_negative_genes)
        test_genes = set(self.pipeline.biosession.proteins).difference(training_positive_genes).difference(training_negative_genes)
        for gene in test_genes:
            gene.add_property("is_testing", 1)

class AddGraphClusterMembership(Operation):
    
    def __init__(self, name="AddGraphClusterMembership"):
        Operation.__init__(self, name)
    
    def __call__(self):
        print "Adding GraphClusterMembership data from SNAP graph cluster algorithm."
        clusters_filename = self.pipeline.communities_filename
        for line in open(clusters_filename, "r"):
            gene_id, cluster_number = line.rstrip().split()
            gene = self.pipeline.biosession.get_protein(gene_id)
            if gene is not None:
                gene.add_evidence(name="graph_cluster_"+cluster_number, value=1, origin_protein=gene_id)

class PlotScoresHist(Operation):
    '''
    Creates a histogram of the machine learning scores for the test data. Allows the user to evaluate if the
    classifier is producing a distribution of scores given the known biology of the disease they are studying.
    For example what proportion of all genes are likely to contribute to causing that disease vs the number the
    classifier is predicting.
    '''
    def __init__(self, name="PlotScoresHist"):
        Operation.__init__(self, name)
    
    def __call__(self):
        print "Plotting ML prediction scores histogram."
        scores = []
        for protein in self.pipeline.biosession.proteins:
            try:
                score = self.pipeline.biosession.ml_controller.get_score(protein.name)
                scores.append(score)
            except KeyError:
                pass
            except TypeError:
                warn("In PlotScoresHist classifier appears to not have score capability (call to ml_controller.get_score() raised TypeError) skipping plot")
                return
        hist, bins = np.histogram(scores, bins=50)
        width = 0.7 * (bins[1] - bins[0])
        center = (bins[:-1] + bins[1:]) / 2
        #clf required because of previous plots by ml_classifier
        plt.clf()
        plt.bar(center, hist, align='center', width=width)
        title_string = "Mito disease causing prediction scores\nbased on network neighbourhoods"
        plt.title(title_string)
        plt.xlabel("Score")
        plt.ylabel("Number of Genes")
        filename = re.sub("\n", "_", re.sub(" ","_",title_string.lower()))+".pdf"
        plt.savefig(filename)
        #self.pipeline.add_resource("ml_scores_list", scores)
        pass

class RunMachineLearning(Operation):
    
    def __init__(self, name="RunMachineLearning"):
        Operation.__init__(self, name)
    
    def __call__(self):
        print "Running Machine Learning stage."
        ml_controller = MLController(self.pipeline.biosession,
                         classifier_list=generate_classifier_list(pipeline.random_seed)
                         #preprocessor=RandomizedPCAPreProcessor(n_components=95),#information_threshold=0.999),
                         #reduce_precision=True
                         )
    
        ml_controller.run(auto_select_classifier=True)
        
        run_desc = self.pipeline.run_desc#"string_10000_communities/"#"barabasi_10000_communities/"
        try:
            fname = os.path.dirname(os.path.abspath(__file__))+"/"+run_desc
            os.makedirs(fname)
        except OSError:
            #folder exists
            pass
        
        try:
            ml_controller.write_prediction_scores_to_file(run_desc)
        except AttributeError:
            warn("Writing scores to file failed")
        for classifier in ml_controller.classifier_list:
            auc_score = str(classifier.roc_auc)[0:4]
            for plot_name, plot in classifier.plots.iteritems():
                    fname = run_desc+classifier.name+"_roc_auc_"+auc_score+"_"+plot_name+".pdf"
                    print "Writing plot:",fname
                    plot.savefig(fname)

class GetCommandlineArgs(Operation):
    '''
    Processes commandline arguments given when running the program and adds
    them to the pipeline as resources e.g. python program.py -fname=foo.txt
    would add a resource called fname with the value foo.txt
    '''
    def __init__(self, name="GetCommandlineArgs"):
        Operation.__init__(self, name)
    
    def __call__(self):
        print "Getting commandline args."
        for item in sys.argv[1:]:
            try:
                name, value = item.split("=")
                if "-" in name:
                    name = re.sub("-", "", name)
                inform("Adding ",name,"with value:",value)
                self.pipeline.add_resource(name, value)
            except AttributeError:
                raise Exception("Could not process commandline argument: "+item)

class WriteTrainingGeneScores(Operation):
    
    def __init__(self, name="WriteTrainingGeneScores"):
        Operation.__init__(self, name)
    
    def __call__(self):
        print "Writing training genes to file."
        out_file = open(self.pipeline.run_desc+"training_protein_scores.txt", "w")
        for gene in self.pipeline.biosession.proteins:
            if gene.has_property("is_training"):
                if gene.get_property("is_training").value  == 1:
                    
                    if gene.get_property("ml_label").value == 1:
                        score = "100"
                    elif gene.get_property("ml_label").value == 0:
                        score = "training_neg"
                    else:
                        raise Exception("training protein has no known ml label:"+str(gene.get_property("ml_label")))
                    output_string = gene.name+"\t"+score+"\n"
                    out_file.write(output_string)

##################################################
#For now these are global functions
##################################################
def build_gene_dict(ensembl_relation_filename):
    '''
    Builds a dictionary mapping ensembl gene ids to the unique gene cluster
    id that ensembl gene belongs too
    '''
    gene_to_unique_id_dict = {}
    
    relation_file = open(ensembl_relation_filename,'r')
    encountered_ensembl_genes = {}
    for line in relation_file:
        ensemble_id, unique_gene_id = line.rstrip().split()
        
        if ensemble_id not in encountered_ensembl_genes:
            
            gene_to_unique_id_dict[ensemble_id] = unique_gene_id
            encountered_ensembl_genes[ensemble_id] = True
    return gene_to_unique_id_dict

def build_unique_to_ensembls_dict(ensembl_relation_filename):
    unique_to_ensembls_dict = collections.defaultdict(list)
    relation_file = open(ensembl_relation_filename,'r')

    for line in relation_file:
        ensemble_id, unique_gene_id = line.rstrip().split()
        unique_to_ensembls_dict[unique_gene_id].append(ensemble_id)
    return unique_to_ensembls_dict

def generate_classifier_list(random_seed=None):
    
    rand_forest = Classifier(RandomForestClassifier(n_estimators=100,
                                                    n_jobs=1,
                                                    random_state=random_seed),
                                                     "rand_forest",
                                                     scoring_method=RandomForestClassifier.predict_proba,
                                                     has_cross_val=False)
    
    c_param = 1
    svc = Classifier(svm.SVC(kernel='linear', C=c_param, probability=True, random_state=random_seed), "SVC_linear_kernel")
    rbf_svc = Classifier(svm.SVC(kernel='rbf', gamma=0.7, C=c_param, probability=True, random_state=random_seed), "SVC_RBF_kernel")
    poly_svc = Classifier(svm.SVC(kernel='poly', degree=3, C=c_param, probability=True, random_state=random_seed), "SVC_polynomial_degree_3")
    
    naive_bayes = Classifier(GaussianNB(), "Gaussian_Naive_Bayes")
    
    ada_boost = Classifier(AdaBoostClassifier(#base_estimator=GaussianNB(),
                                              n_estimators=100,
                                              learning_rate=1.0,
                                              algorithm='SAMME.R',
                                              random_state=random_seed), "AdaBoostClassifier")
    
    k_nearest = Classifier(KNeighborsClassifier(n_neighbors=15), "KNeighborsClassifier")
    
    #lin_svc = Classifier(svm.LinearSVC(C=c_param, random_state=random_seed), "LinearSVC_linear_kernel")
    #classifier_list = [k_nearest, ada_boost, rand_forest, svc, rbf_svc, poly_svc, naive_bayes]
    classifier_list = [rand_forest, ada_boost]
    return classifier_list


##################################################
#Graph Cluster Pipeline
##################################################
class GraphClusterPipeline(Pipeline):
    
    def __init__(self):
        Pipeline.__init__(self)
        #Speeds up runtime for testing but gives unusable results
        self.add_resource("testing", False)
        
        ##################################################
        #Add resources reqired for the operations of the pipeline
        ##################################################
        #Add the Biosession to the pipeline
        #need to instantiate biosession before we can set the output dir as we need it's timestamp
        self.add_resource("biosession", BioSession("ML_session") )
        self.add_resource("random_seed", 0)
        random.seed(self.random_seed)
        
        training_pos_folder = "resources/training_data/training_positives/"
        
        # Hereditary motor neurone disease
        #self.add_resource("train_positives_fname", training_pos_folder+"hmn_disease_genes/hmn_gene_list_as_ensps.txt")
        
        # The Washington University mitochondrial disease genes list
        self.add_resource("train_positives_fname", training_pos_folder+"mito_disease_genes/wash_u/wash_u_extras_ensps.txt")
        
        # IMPI training positives 
        #self.add_resource("train_positives_fname", training_pos_folder+"impi_training_positive/training_positives_as_ensps.txt")
        
        # DEPRECIATED Was used when running MIDAS on Barabasi's network which needed NCBI ids not ENSP ids
        #self.add_resource("train_positives_fname", training_pos_folder+"/mito_disease_genes_as_ncbi_ids_for_barabasi.txt")
        
        
        ##################################################
        #Add operations to the pipeline
        ##################################################
        #Ordering of operations is important
        self.add_operation( GetCommandlineArgs() )
        self.add_operation( AddGenesToSession() )
        self.add_operation( AddGraphClusterMembership() )
        self.add_operation( AddTrainingPositiveStatus() )
        self.add_operation( AddTrainingNegativeStatus() )
        self.add_operation( AddTestStatus() )
        self.add_operation( RunMachineLearning() )
        self.add_operation( WriteTrainingGeneScores() )
        self.add_operation( PlotScoresHist() )
        

    def get_output_dir(self):
        '''
        Appends _TESTING_RUN to output folder path if pipeline is running in testing more
        so that the user can see at a glance whether a folder is testing or a real run.
        '''
        
        end_string = "_TESTING_RUN/" if self.testing else "/"
        return self.pwd+"/results/"+self.biosession.timestamp+end_string

def move_file(from_filename, to_filename):
    command_list = ["mv", from_filename, to_filename]
    cmd_string = subprocess.list2cmdline(command_list)
    
    return_value = subprocess.call(command_list)#, stdout=png_file)

    if return_value !=0:
        output_string = subprocess.check_output(command_list)
        raise Exception("move_file() failed with output: "+output_string)

def call_bigclam(graph_fname, time_stamp, n_clusters):
    #png_filename = self.pipeline.output_dir+"images/"+plot_name+".png"

    print "Running BIGCLAM"
    #TODO: This is a bit messy refactor when possible
    output_folder = "results/"+time_stamp+"/string_"+str(n_clusters)+"_communities/"
    output_prefix = str(n_clusters)+"_"
    command_list = ["bigclam", "-i:"+graph_fname, "-c:"+str(n_clusters), "-nt:8"]
    cmd_string = subprocess.list2cmdline(command_list)
    
    return_value = subprocess.call(command_list)#, stdout=png_file)

    if return_value !=0:
        output_string = subprocess.check_output(command_list)
        raise Exception("call_bigclam() failed with output: "+output_string)
    
    # Create the folder to put the results in
    fname = os.path.dirname(os.path.abspath(__file__))+"/"+output_folder
    os.makedirs(fname)
    
    #Move the file from the default output name to the name with the number of clusters to prevent overwriting
    output_filename = output_folder+output_prefix+"cmtyvv.txt"
    move_file("cmtyvv.txt", output_filename)
    
    #remove this file which is output by BIGCLAM but unused by us
    subprocess.call(["rm", "graph.gexf"])
    
    return output_filename

def reformat_communities(in_fname, out_fname):
    '''Reformats the communities file that comes out of bigclam'''
    out_lines = []
    for i, line in enumerate(open(in_fname, "r")):
        if line.startswith("#"):
            continue
        for node_id in line.rstrip().split():
            out_lines.append(node_id+"\t"+str(i)+"\n")
    
    out_file = open(out_fname,"w")
    for line in out_lines:
        out_file.write(line)

def extract_graph_archive(graph_folder, graph_archive_fname):
    '''
    This is the temp quick fix to decompressing the graph archive which has to
    be compressed to fit on github.
    
    Code is in progress to check hashes etc. to prevent decompressing when the
    file is already decompressed but this is fine for now.
    '''
    print "Extracting compressed network file:",graph_archive_fname,"...",
    tar = tarfile.open(graph_folder+graph_archive_fname)
    tar.extractall(path=graph_folder)
    tar.close()
    print "complete"

if __name__ == '__main__':
    '''
    
    TODO: Plan for todo: move calling of bigclam and reformat_communities into pipeline with their resources added as resources to the pipeline
          update the resources that change i.e. number of clusters and have run_desc updated for each call of run_pipeline()
    
    TODO: check for -h or --help args and print options
    TODO: handle clever network fix for decompressing graph, check md5sum of output graph from network package file
    TODO: more user friendly options for running the pipeline
    TODO: select for the user the optimal number of network communities based on machine learning performance
    TODO: put pdf plot into correct results folder, perhaps add results folder path as resource
    TODO: create ROC AUC vs number of communities
    '''
    
    #TODO: This is a temp solution see extract_graph_archive() function body for more info
    graph_folder = "resources/networks/string/"
    graph_fname = "no_scores_non_textmining_edges_9606.protein.links.detailed.v10.txt"
    graph_archive_fname = graph_fname+".tar.gz"
    extract_graph_archive(graph_folder, graph_archive_fname)
    
    time_stamp = get_date_time()
    
    for number_of_clusters in [10, 50, 200, 300, 400, 500, 1000, 3000, 5000, 7000, 9000]:
        
        bigclam_output_filename = call_bigclam(graph_folder+graph_fname, time_stamp, number_of_clusters)
        
        data_folder = "/mallow/data/2year/mito_graph/v10_raw_string_downloads/clustering/"
        communities_fname = data_folder+"string_v10_no_textmining_reformatted_"+str(number_of_clusters)+"_communities.txt"
        
        reformat_communities(bigclam_output_filename, communities_fname)
        
        print "Starting Machine learning stage"
        pipeline = GraphClusterPipeline()
        pipeline.add_resource("communities_filename", communities_fname)
        pipeline.add_resource("run_desc", "results/"+time_stamp+"/string_"+str(number_of_clusters)+"_communities/")
        pipeline.run_pipeline()

    #scores = pipeline.ml_scores_list
    #print "N genes:",len(scores)
    #print "N above 0.5",len([x for x in scores if x > 0.5])
    #print "N above 0.75",len([x for x in scores if x > 0.75])
    #print "N above 0.9",len([x for x in scores if x > 0.9])
    inform("Pipeline completed")
    
